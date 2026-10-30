"""Frozen texture eligibility, method selection and native local contrast diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from deep_sky_siril_contract import (RUN_ID_PATTERN, SKILL_ROOT, atomic_write_json,
    fingerprint_matches, load_json, protocol_reference_bindings)

import numpy as np

from deep_sky_siril_artifacts import read_scientific_pixels, read_scientific_header, read_fits_header
from deep_sky_siril_contract import ContractError, fingerprint, load_json, session_path, sha256_file
from siril_manual_bundle import BundleError, strict_json_bytes

POLICY = 'texture_hf_review_v1'
MULTISCALE_POLICY = 'wavelet_clahe_competition_v2'
HDR_POLICY = 'wavelet_only_v1'
SCALES = (1, 2, 4, 8)
MIN_TEXTURE = 1.7
HF_WARNING = 1.5
ENERGY_FLOOR = 1e-12


def fail(message):
    raise ContractError('processing_contract_invalid', message)


def bound_file(session, reference, roots, *, exists=True):
    if not isinstance(reference, str) or not reference or reference.startswith('/'):
        fail('Local contrast evidence requires a session-relative path')
    try:
        return session_path(session, reference, must_exist=exists, allowed_roots=roots)
    except OSError as exc:
        fail(f'Local contrast evidence is missing or unsafe: {reference}: {exc}')


def lineage_binding(session, source, lineage, *, policy=POLICY, hdr_policy=None):
    """Capture already verified ancestors; replay never enumerates current receipts."""
    if not lineage:
        fail('Local contrast requires a verified scientific lineage')
    bindings = []
    current, seen, outputs = '@input', set(), {'@input'}
    for receipt in lineage:
        path = bound_file(session, f"runs/{receipt['id']}.json", ('runs',))
        captured = fingerprint(path, include_path=False)
        if load_json(path) != receipt or fingerprint(path, include_path=False) != captured:
            fail('Local contrast ancestor changed while binding its lineage')
        if receipt['id'] in seen or receipt.get('source_path') != current:
            fail('Local contrast scientific lineage contains a cycle or inconsistent parent')
        if receipt.get('status') != 'success' or not receipt.get('primary_output'):
            fail('Local contrast requires successful primary ancestors')
        if receipt.get('hdr_transfer') and hdr_policy != HDR_POLICY:
            fail(f"HDR and CLAHE are mutually exclusive on the current lineage: {path.name}; preserve the accepted parent")
        if policy == MULTISCALE_POLICY and receipt.get('protocol') == 'structure.local-contrast':
            fail('Only one local enhancement is allowed on a scientific lineage; compare siblings from the same parent')
        seen.add(receipt['id'])
        current = receipt['primary_output']
        bound_file(session, current, ('artifacts',))
        if current in outputs:
            fail('Local contrast scientific lineage contains a repeated primary or cycle')
        outputs.add(current)
        bindings.append({'path': path.relative_to(session).as_posix(), **captured})
    if current != source.relative_to(session).as_posix():
        fail('Local contrast lineage does not own the declared scientific parent')
    return bindings


def replay_lineage(session, source, bindings, *, policy=POLICY, hdr_policy=None):
    receipts = []
    for record in bindings:
        path = bound_file(session, record['path'], ('runs',))
        if record != {'path': record['path'], **fingerprint(path, include_path=False)}:
            fail(f'Local contrast ancestor fingerprint changed: {record["path"]}')
        receipts.append(load_json(path))
    if lineage_binding(session, source, receipts, policy=policy, hdr_policy=hdr_policy) != bindings:
        fail('Local contrast lineage changed')
    return receipts


def method_eligibility(lineage):
    hdr = [receipt['id'] for receipt in lineage if receipt.get('hdr_transfer')]
    return {'policy': HDR_POLICY, 'hdr_runs': hdr,
            'wavelet': {'allowed': True, 'reason': 'eligible'},
            'clahe': {'allowed': not hdr, 'reason': 'hdr_lineage' if hdr else 'eligible'}}


def eligible_candidates(choices, methods):
    return {key: value for key, value in choices.items() if methods[value['method']]['allowed']}


def _regions(session, source, reference):
    from deep_sky_siril_metrics import measure_regions
    from deep_sky_siril_background import intersects
    path = bound_file(session, reference, ('reports',))
    raw = path.read_bytes()
    try:
        report = measure_regions(source, None, path, scientific=True)
        doc = strict_json_bytes(raw, document='local contrast regions')
    except (ValueError, OSError, BundleError) as exc:
        fail(str(exc))
    if report['status'] != 'measured':
        fail('Local contrast regions require finite, geometry-bound scientific measurements')
    regions = doc['regions']
    sky = [r for r in regions if r['role'] == 'background']
    targets = [r for r in regions if r['role'] == 'faint']
    if len(sky) < 3 or not targets:
        fail('Local contrast requires at least three independent background ROIs and one faint texture ROI')
    w, h = doc['reference']['width'], doc['reference']['height']
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        if (x1-x0)*(y1-y0) < 64:
            fail(f"Local contrast ROI {region['id']} requires at least 64 finite pixels")
        if region['role'] not in {'background', 'faint'}:
            continue
        context = [x0-32, y0-32, x1+32, y1+32]
        if context[0] < 0 or context[1] < 0 or context[2] > w or context[3] > h:
            fail(f"Local contrast ROI {region['id']} has a 4-sigma filtering context outside the image")
        for protected in regions:
            if protected['role'] in ({'core', 'faint', 'stars'} if region['role'] == 'background' else {'core', 'stars'}):
                tested = context if region['role'] == 'background' else region['rect']
                if intersects(tested, protected['rect']):
                    fail(f"Local contrast ROI {region['id']} intersects protected region {protected['id']}")
    for index, region in enumerate(sky):
        if any(intersects(region['rect'], other['rect']) for other in sky[:index]):
            fail(f"Local contrast background ROI {region['id']} overlaps another sky ROI")
    if path.read_bytes() != raw:
        fail('Local contrast regions changed during validation')
    return doc, {'path': reference, **fingerprint(path, include_path=False)}


def energies(values, regions, scales=SCALES):
    """Measure full-resolution crops with real context, never synthetic padding."""
    rows = []
    height = values.shape[1]
    for region in regions:
        if region['role'] not in {'background', 'faint'}:
            continue
        x0, y0, x1, y1 = region['rect']
        for sigma in scales:
            radius = 4*sigma
            rgb = np.asarray(values[:, height-y1-radius:height-y0+radius,
                                    x0-radius:x1+radius], dtype=np.float64)
            lum = rgb[0] if rgb.shape[0] == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], rgb)
            positions = np.arange(-radius, radius+1, dtype=np.float64)
            kernel = np.exp(-.5*(positions/sigma)**2)
            kernel /= kernel.sum()
            smooth = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode='valid'), 1, lum)
            smooth = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode='valid'), 0, smooth)
            energy = float(np.mean(np.abs(lum[radius:-radius, radius:-radius]-smooth)))
            if not np.isfinite(energy):
                fail(f"Nonfinite high-frequency measurement: {region['id']}, sigma={sigma}")
            rows.append({'id': region['id'], 'role': region['role'], 'sigma': sigma, 'amplitude': energy})
    return rows


def texture(rows, *, diagnostic_only=False):
    floor = float(np.median([r['amplitude'] for r in rows if r['role'] == 'background' and r['sigma'] == 2]))
    if not np.isfinite(floor) or floor <= ENERGY_FLOOR:
        fail('Local contrast background noise floor is unavailable or at most 1e-12; preserve the parent')
    ratios = [{'id': r['id'], 'ratio': r['amplitude']/floor}
              for r in rows if r['role'] == 'faint' and r['sigma'] == 2]
    if not ratios or any(not np.isfinite(r['ratio']) or r['ratio'] < 0 for r in ratios):
        fail('Local contrast requires finite texture measurements in every faint ROI')
    minimum = min(ratios, key=lambda r: r['ratio'])
    if not diagnostic_only and minimum['ratio'] < MIN_TEXTURE:
        fail(f"texture_snr below 1.7: ROI={minimum['id']}, texture_snr={minimum['ratio']!r}; "
             f"background_amplitude={floor!r}, subject_ratios={ratios!r}; "
             "local contrast blocked (engineering texture ratio, not physical SNR); preserve the parent")
    result = {'sigma': 2, 'background_amplitude': floor, 'ratios': ratios,
              'texture_snr': minimum['ratio'], 'minimum': MIN_TEXTURE}
    if diagnostic_only:
        result.update(role='diagnostic_only', warnings=[
            {**row, 'code': 'low_texture_ratio', 'threshold': MIN_TEXTURE}
            for row in ratios if row['ratio'] < MIN_TEXTURE])
    return result


def _source(source):
    record = fingerprint(source, include_path=False)
    values, selection = read_scientific_pixels(source)
    if values.shape[0] != 3 or not np.isfinite(values).all():
        fail('Local contrast requires finite RGB scientific pixels')
    if fingerprint(source, include_path=False) != record:
        fail('Local contrast scientific source changed during decoding')
    return values, selection


def prepare(session, source, primary, commands, reference, lineage, *, policy=POLICY, text=None, diagnostic_only=False, hdr_policy=None):
    from deep_sky_siril_validation import _normalized_write
    ancestors = lineage_binding(session, source, lineage, policy=policy, hdr_policy=hdr_policy)
    declaration = next((line.split(':', 1)[1] for line in (text or '').splitlines() if line.startswith('# local-contrast:')), '{}')
    declared = strict_json_bytes(declaration.encode(), document='local contrast')
    methods = method_eligibility(lineage) if hdr_policy == HDR_POLICY else None
    if methods and not methods[declared.get('method', 'clahe')]['allowed']:
        fail('HDR and CLAHE are mutually exclusive; only a bound wavelet candidate is eligible')
    doc, regions = _regions(session, source, reference)
    before, selection = _source(source)
    measured = energies(before, doc['regions'])
    eligibility = texture(measured, diagnostic_only=diagnostic_only)
    previews, current = {}, None
    saves = [row for row in commands if row[0] == 'save']
    variant = 'method' in declared
    mask = primary.with_name(primary.stem+'-mask.fit') if variant else _normalized_write('save', saves[2][1])
    for row in commands:
        if row[0] == 'load':
            current = _normalized_write('save', row[1])
        elif row[0] == 'save':
            current = _normalized_write('save', row[1])
        elif row[0] in {'pm', 'clahe', 'gauss', 'rgbcomp', 'wavelet', 'wrecons'}:
            current = None
        elif row[0] == 'savejpg':
            role = 'parent' if current == source else 'candidate' if current == primary else 'mask' if current == mask else None
            if role is None or role in previews or len(row) != 3 or row[2] != '95':
                fail('Local contrast requires distinct direct parent, mask and candidate previews at quality 95')
            preview = session_path(session, _normalized_write('savejpg', row[1]), allowed_roots=('previews',))
            previews[role] = preview.relative_to(session).as_posix()
    if set(previews) != {'parent', 'mask', 'candidate'} or len(set(previews.values())) != 3:
        fail('Local contrast requires direct parent, mask and candidate previews')
    if sha256_file(source) != selection['sha256'] or fingerprint(bound_file(session, reference, ('reports',)), include_path=False) != {k: regions[k] for k in ('sha256', 'size')}:
        fail('Local contrast source or regions changed during preparation')
    result = {'policy': policy, 'regions': regions, 'scientific_source': selection,
            'lineage': ancestors, 'eligibility': eligibility, 'parent_amplitudes': measured, 'previews': previews}
    if diagnostic_only:
        result['texture_diagnostic_only'] = True
    if methods:
        result['method_eligibility'] = methods
    if variant:
        result['native'] = native_paths(session, primary, 'wavelet' if any(r[0] == 'wavelet' for r in commands) else 'clahe',
                                        protected='mask_support' in strict_json_bytes(declaration.encode(), document='local contrast'))
        result['parameters'] = parameter_binding(session, source, reference, text)
        if declared.get('subject_mask'):
            from deep_sky_siril_subject_mask import load as load_subject_mask
            result['subject_mask'] = load_subject_mask(session,source,declared['subject_mask'])[1]
    return result


def measure(session, source, primary, binding):
    from deep_sky_siril_processing import complete_wcs, wcs_matrix, finite_number
    from deep_sky_siril_metrics import analyze_image_histogram, evaluate_stage_gates
    methods = binding.get('method_eligibility')
    lineage = replay_lineage(session, source, binding['lineage'], policy=binding['policy'],
                             hdr_policy=methods.get('policy') if methods else None)
    if methods and (methods != method_eligibility(lineage)
                    or not methods[binding.get('native', {}).get('method', 'clahe')]['allowed']):
        fail('Local contrast method eligibility changed')
    doc, regions = _regions(session, source, binding['regions']['path'])
    before, selection = _source(source)
    after, candidate_selection = _source(primary)
    measured = energies(before, doc['regions'])
    if (binding['policy'] not in {POLICY, MULTISCALE_POLICY} or regions != binding['regions'] or selection != binding['scientific_source']
        or measured != binding['parent_amplitudes']
        or texture(measured, diagnostic_only=binding.get('texture_diagnostic_only', False)) != binding['eligibility']):
        fail('Local contrast frozen eligibility, source selection or region binding changed')
    if before.shape != after.shape:
        fail('Local contrast candidate geometry changed')
    if complete_wcs(source):
        h, k = read_scientific_header(source), read_fits_header(primary)
        keys = ['CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2'] + [key for key in h if key.startswith(('A_', 'B_', 'AP_', 'BP_'))]
        if (not complete_wcs(primary) or any(key not in k or not np.isclose(finite_number(h[key]), finite_number(k[key]), rtol=1e-12, atol=1e-10) for key in keys)
            or not np.allclose(wcs_matrix(h), wcs_matrix(k), rtol=1e-12, atol=1e-15)
            or any(str(h.get(key, 'deg')).casefold() != str(k.get(key, 'deg')).casefold() for key in ('CTYPE1', 'CTYPE2', 'CUNIT1', 'CUNIT2'))):
            fail('Local contrast changed or lost the existing WCS')
    candidate = energies(after, doc['regions'])
    comparisons, warnings, unavailable = [], [], []
    for parent_row, candidate_row in zip(measured, candidate):
        ratio = candidate_row['amplitude']/parent_row['amplitude'] if parent_row['amplitude'] > ENERGY_FLOOR else None
        if ratio is not None and not np.isfinite(ratio):
            ratio = None
        row = {k: parent_row[k] for k in ('id', 'role', 'sigma')}
        row.update(parent_amplitude=parent_row['amplitude'], candidate_amplitude=candidate_row['amplitude'], amplification=ratio)
        comparisons.append(row)
        if ratio is None:
            unavailable.append({**row, 'reason': 'parent amplitude at most 1e-12 or nonfinite ratio'})
        elif ratio > HF_WARNING:
            warnings.append({**row, 'code': 'high_frequency_amplification_exceeded', 'threshold': HF_WARNING})
    histogram = analyze_image_histogram(primary)
    _, clipping_verdict = evaluate_stage_gates('structure.local-contrast', histogram)
    if (sha256_file(source) != selection['sha256'] or sha256_file(primary) != candidate_selection['sha256']
        or regions != {'path': regions['path'], **fingerprint(bound_file(session, regions['path'], ('reports',)), include_path=False)}):
        fail('Local contrast scientific pixels or regions changed during measurement')
    result = {'policy': binding['policy'], 'status': 'uncertain' if unavailable else 'measured',
            'requires_visual_review': True, 'geometry_unchanged': True,
            'wcs': 'preserved' if complete_wcs(source) else 'absent_or_incomplete',
            'scientific_sources': {'parent': selection, 'candidate': candidate_selection},
            'coordinate_frame': 'preview_top_left_zero_based_half_open',
            'amplitude_definition': 'mean_abs_luminance_minus_gaussian_full_resolution',
            'comparisons': comparisons, 'warnings': warnings, 'unavailable': unavailable,
            'warning_threshold': HF_WARNING, 'clipping_verdict': clipping_verdict}
    if binding.get('texture_diagnostic_only'):
        result['texture_diagnostics'] = binding['eligibility']
        warnings.extend(binding['eligibility']['warnings'])
    if binding.get('native'):
        result['native'] = measure_native(session, source, primary, binding)
    return result


def verify_receipt(session, receipt):
    from deep_sky_siril_processing import script_commands, validate_local_contrast
    for record in receipt['outputs']:
        path = bound_file(session, record['path'], ('artifacts', 'previews', 'reports'))
        if any(record.get(key) != value for key, value in fingerprint(path, include_path=False).items()):
            return False
    source = bound_file(session, receipt['source_path'], ('artifacts',))
    primary = bound_file(session, receipt['primary_output'], ('artifacts',))
    script = bound_file(session, receipt['script']['path'], ('scripts',))
    text = script.read_text(encoding='utf-8')
    declarations = [line.split(':', 1)[1] for line in text.splitlines() if line.startswith('# local-contrast:')]
    if len(declarations) != 1:
        return False
    declaration = strict_json_bytes(declarations[0].encode(), document='local contrast declaration')
    reference = declaration.pop('regions')
    commands = script_commands(text)
    if validate_local_contrast(session, source, primary, commands, declaration) != receipt.get('local_contrast'):
        return False
    binding = receipt['local_contrast_guard']
    hdr_policy = load_json(session/'session.json')['execution_policy'].get('local_contrast_hdr')
    replay_lineage(session, source, binding['lineage'], policy=binding['policy'], hdr_policy=hdr_policy)
    lineage = [load_json(bound_file(session, r['path'], ('runs',))) for r in binding['lineage']]
    diagnostic_only = load_json(session/'session.json')['execution_policy'].get('subject_enhancement') == 'benefit_first_v1'
    return (prepare(session, source, primary, commands, reference, lineage, policy=binding['policy'], text=text,
                    diagnostic_only=diagnostic_only, hdr_policy=hdr_policy) == binding
            and measure(session, source, primary, binding) == receipt.get('processing_checks', {}).get('local_contrast'))


# Shared native recipes keep trials, formal scripts and replay on the same arithmetic.

PARAMETERS_SCHEMA = 'starun-siril.local-contrast-parameters.v1'
REQUEST_SCHEMA = 'starun-siril.local-contrast-request.v1'
PROPOSAL_REQUEST = 'starun-siril.local-contrast-request.v2'
PROTECTED_REQUEST = 'starun-siril.local-contrast-request.v3'
SPATIAL_REQUEST = 'starun-siril.local-contrast-request.v4'
LEGACY_HELPER = {'sha256': '2caf3d692ebbc6e866278581efd71ccb4a117633821176b0edcf0e9de3610633', 'size': 44131}
PRIOR_HELPER = {'sha256': 'e60f3d6b99f8de065412068444e9f1be482de7584acd1411708102c0c77bebd2', 'size': 47215}
STRICT_TEXTURE_HELPER = {'sha256': '3ceaa201493118707ce91b5aee71ef5a5c2bca0efcb41cd1558528b34f7cdc87', 'size': 49399}
PRE_HDR_HELPER = {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748', 'size': 50689}
WEIGHTS = {f'wavelet-{n}': [1+.02*t, 1+.04*t, 1+.12*t, 1+.08*t, 1., 1.]
           for n, t in [('050', .5), ('100', 1.), ('150', 1.5)]}
FINE_OFF = [1., 1., 1.12, 1.08, 1., 1.]
BOUNDS = ('low_start', 'low_end', 'high_start', 'high_end')


def wavelet_coefficients(amplitude, fine_detail):
    return [1+delta*amplitude for delta in
            ((.02, .04) if fine_detail else (0., 0.)) + (.12, .08, 0., 0.)]


def request_candidates(request, *, fine_off=False, mask_support='source_exclusion_v2'):
    if not isinstance(request, dict) or request.get('schema') not in {REQUEST_SCHEMA, PROPOSAL_REQUEST, PROTECTED_REQUEST, SPATIAL_REQUEST}:
        fail('Invalid local contrast request')
    spatial = request['schema'] == SPATIAL_REQUEST
    proposal = request['schema'] in {PROPOSAL_REQUEST, PROTECTED_REQUEST, SPATIAL_REQUEST}
    required = {'schema', 'source', 'regions', 'mask', 'manual_lookup'} | ({'wavelet', 'clahe'} if proposal else set())
    if spatial:
        required.add('subject_mask')
    if set(request) != required or not isinstance(request['mask'], dict):
        fail('Unknown or missing local contrast request fields')
    mask = request['mask']
    if set(mask) != ({'high_start','high_end','sigma'} if spatial else {*BOUNDS,'sigma'}):
        fail('Local contrast requests require exactly four mask bounds and sigma')
    if proposal:
        wavelet, clahe = request['wavelet'], request['clahe']
        if (not isinstance(wavelet, dict) or set(wavelet) != {'amplitude', 'fine_detail'}
            or type(wavelet['amplitude']) not in (int, float) or not np.isfinite(wavelet['amplitude'])
            or not 0 < wavelet['amplitude'] <= 1.5 or type(wavelet['fine_detail']) is not bool
            or not isinstance(clahe, dict) or set(clahe) != {'strength'}):
            fail('Propose a finite wavelet amplitude in (0, 1.5], a fine_detail boolean and CLAHE strength')
        if fine_off and not wavelet['fine_detail']:
            fail('Fine detail is already disabled; no further fine-off revision is allowed')
        weights = {'wavelet': wavelet_coefficients(wavelet['amplitude'], wavelet['fine_detail'])}
        strength = clahe['strength']
    else:
        weights, strength = WEIGHTS, .2
    choices = {key: {**mask, 'method': 'wavelet', 'coefficients': coefficients}
               for key, coefficients in weights.items()}
    choices['clahe'] = {**mask, 'method': 'clahe', 'cliplimit': 1.5, 'grid': 8, 'strength': strength}
    if request['schema'] in {PROTECTED_REQUEST, SPATIAL_REQUEST}:
        for parameters in choices.values():
            parameters['mask_support'] = mask_support
            if spatial:
                parameters['subject_mask'] = request['subject_mask']
    for parameters in choices.values():
        variant_parameters(parameters)
    if fine_off:
        coefficients = wavelet_coefficients(wavelet['amplitude'], False) if proposal else FINE_OFF
        return {'fine-off': {**choices['wavelet' if proposal else 'wavelet-100'], 'coefficients': coefficients}}
    return choices


def variant_parameters(value):
    if not isinstance(value, dict) or value.get('method') not in {'wavelet', 'clahe'}:
        fail('Declare a native wavelet or clahe method')
    method = value['method']
    spatial = 'subject_mask' in value
    bounds = ('high_start','high_end') if spatial else BOUNDS
    keys = {'method', *bounds, 'sigma'} | ({'coefficients'} if method == 'wavelet' else {'cliplimit', 'grid', 'strength'})
    if 'mask_support' in value:
        if value['mask_support'] not in ('source_exclusion_v1', 'source_exclusion_v2'):
            fail('Unknown local contrast mask support')
        keys.add('mask_support')
    if spatial:
        keys.add('subject_mask')
        if not isinstance(value['subject_mask'],dict) or value.get('mask_support') not in ('source_exclusion_v1', 'source_exclusion_v2'):
            fail('Spatial local contrast requires a bound mask report and protected support')
    if set(value) != keys:
        fail('Unknown or missing explicit local contrast parameters')
    for name in (*bounds, 'sigma'):
        if type(value[name]) not in (int, float) or not np.isfinite(value[name]):
            fail('Local contrast mask values must be finite JSON numbers')
    if spatial:
        from deep_sky_siril_subject_mask import highlight
        highlight(value)
        a,b,c,d = 0.,0.,value['high_start'],value['high_end']
    else:
        a,b,c,d = [value[k] for k in BOUNDS]
    if (not spatial and not 0 <= a < b <= c < d <= 1) or value['sigma'] != 2:
        fail('Explicit local contrast requires ordered mask bounds and sigma=2')
    if method == 'wavelet':
        coefficients = value['coefficients']
        if (not isinstance(coefficients, list) or len(coefficients) != 6
            or any(type(v) not in (int, float) or not np.isfinite(v) for v in coefficients)):
            fail('Wavelets require six finite coefficients')
        amplitude = (coefficients[2]-1)/.12
        if (not 0 <= amplitude <= 1.5 or coefficients[4:] != [1., 1.]
            or not any(np.allclose(coefficients, wavelet_coefficients(amplitude, fine), rtol=0, atol=1e-12)
                       for fine in (True, False))):
            fail('Wavelets require bounded proportional coefficients, with the fifth detail and residual unchanged')
    elif (type(value['grid']) is not int or value['grid'] != 8
          or type(value['cliplimit']) not in (int, float) or value['cliplimit'] != 1.5
          or type(value['strength']) not in (int, float) or not np.isfinite(value['strength'])
          or not 0 < value['strength'] <= .5):
        fail('Competition CLAHE requires cliplimit=1.5, grid=8 and finite strength in (0, 0.5]')
    return value


def native_paths(session, primary, method, *, protected=False):
    stem = primary.stem
    names = ['r', 'g', 'b', 'l', 'l-enhanced', 'mask', 'gain', 'r-enhanced', 'g-enhanced', 'b-enhanced']
    if protected:
        names += ['mask-base', 'mask-smoothed']
    if method == 'wavelet':
        names += ['identity', *[f'plane-{i}' for i in range(1, 7)]]
    return {'method': method, 'files': {name: primary.with_name(f'{stem}-{name}.fit').relative_to(session).as_posix() for name in names}}


def recipe(session, source, primary, parameters, *, mask_session=None):
    p = variant_parameters(parameters)
    paths = native_paths(session, primary, p['method'], protected='mask_support' in p)['files']
    f = lambda name: session/paths[name]
    v = lambda name: '$'+str(f(name).relative_to(session).with_suffix(''))+'$'
    q = lambda path: json.dumps(str(path))
    save = lambda name: 'save '+q(f(name))+' -chksum'
    rows = ['load '+q(source), 'savejpg '+q(session/'previews'/f'{primary.stem}-parent')+' 95',
            'split '+' '.join(q(f(n).with_suffix('')) for n in ('r', 'g', 'b')),
            'pm '+q(f'0.2126 * {v("r")} + 0.7152 * {v("g")} + 0.0722 * {v("b")}'), save('l')]
    if p['method'] == 'wavelet':
        rows += ['wavelet 6 2', 'wrecons 1 1 1 1 1 1', save('identity')]
        for i in range(6):
            rows += ['wrecons '+' '.join(str(int(i == j)) for j in range(6)), save(f'plane-{i+1}')]
        rows += ['wrecons '+' '.join(format(float(c), '.15g') for c in p['coefficients'])]
    else:
        rows += ['clahe 1.5 8']
    rows += [save('l-enhanced')]
    spatial_file = None
    if p.get('subject_mask'):
        from deep_sky_siril_subject_mask import load as load_subject_mask
        mask_root = Path(mask_session) if mask_session is not None else session
        spatial = load_subject_mask(mask_root,source,p['subject_mask'])[0]
        spatial_file = mask_root/spatial['mask']['path']
        a,b,c,d = 0.,1.,p['high_start'],p['high_end']
    else:
        a,b,c,d = [p[k] for k in BOUNDS]
    t = f'min(1, max(0, ({v("l")} - {a}) / {b-a}))'
    peak = f'max({v("r")}, max({v("g")}, {v("b")}))'
    u = f'min(1, max(0, ({peak} - {c}) / {d-c}))'
    mask = f'({t}^2 * (3 - 2 * {t})) * (1 - {u}^2 * (3 - 2 * {u}))'
    if spatial_file is not None:
        mask = f'(1 - {u}^2 * (3 - 2 * {u}))'
    strength = p.get('strength', 1.)
    rows += ['pm '+q(mask)]
    if 'mask_support' in p:
        effective = (f'min({v("mask-base")}, {v("mask-smoothed")})' if p['mask_support'] == 'source_exclusion_v2'
                     else f'{v("mask-base")} * {v("mask-smoothed")}')
        if spatial_file is not None:
            from deep_sky_siril_processing import _variable
            effective += f' * {_variable(spatial_file,session)}'
        rows += [save('mask-base'), 'gauss 2', save('mask-smoothed'), 'pm '+q(effective)]
    else:
        rows += ['gauss 2']
    rows += [save('mask'),
             'savejpg '+q(session/'previews'/f'{primary.stem}-mask')+' 95',
             'pm '+q(f'1 + {strength} * {v("mask")} * ({v("l-enhanced")} - {v("l")}) / max({v("l")}, 0.000001)'), save('gain')]
    for name in ('r', 'g', 'b'):
        rows += ['pm '+q(f'{v(name)} * {v("gain")}'), save(name+'-enhanced')]
    rows += ['rgbcomp '+' '.join(q(f(n+'-enhanced')) for n in ('r', 'g', 'b'))+' '+q('-out='+str(primary.with_suffix(''))),
             'load '+q(primary), 'stat main', 'savejpg '+q(session/'previews'/primary.stem)+' 95', 'close']
    return rows


def validate_variant(session, source, primary, commands, value):
    from deep_sky_siril_processing import script_commands
    import re
    parameters = variant_parameters(value)
    expected = script_commands('\n'.join(['requires 1.4.4 1.5.0', 'set32bits', *recipe(session, source, primary, parameters)]))
    def normalized(rows):
        return [[re.sub(r'(?<![A-Za-z0-9_/])(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',
                        lambda m: format(float(m[0]), '.12g'), word) for word in row] for row in rows]
    if normalized(commands) != normalized(expected):
        fail('Local contrast must exactly match its native luminance recipe and protected common RGB gain')
    return parameters


def _record(session, path):
    return {'path': path.relative_to(session).as_posix(), **fingerprint(path, include_path=False)}


def load_parameters(session, path):
    try:
        return _load_parameters(session, path)
    except (KeyError, TypeError, ValueError, OSError, BundleError) as exc:
        fail('Invalid or missing local contrast parameter evidence: '+str(exc))


def _load_parameters(session, path):
    from deep_sky_siril_session import load_session, known_execution_policy
    payload = load_session(session)[1]
    path = session_path(session, path, must_exist=True, allowed_roots=('reports',))
    report = strict_json_bytes(path.read_bytes(), document='local contrast parameters')
    if (report.get('schema') != PARAMETERS_SCHEMA or report.get('policy') != MULTISCALE_POLICY
        or payload['execution_policy'].get('local_contrast') != MULTISCALE_POLICY
        or report.get('session_sha256') != payload['session_sha256'] or report.get('requires_visual_review') is not True):
        fail('Local contrast parameter report belongs to a different session or policy')
    bindings = report['bindings']
    hdr_policy = payload['execution_policy'].get('local_contrast_hdr')
    diagnostic_only = payload['execution_policy'].get('subject_enhancement') == 'benefit_first_v1'
    if hdr_policy is None and diagnostic_only and bindings['helper'] != {
            'path': 'scripts/deep_sky_siril_local_contrast.py', **PRE_HDR_HELPER}:
        fail('Local contrast parameter binding changed: helper')
    legacy_helper = False
    prior_helper = False
    for name in ('source', 'regions', 'request', 'helper'):
        record = bindings[name]
        file = Path(__file__).resolve() if name == 'helper' else bound_file(session, record['path'], ('reports', 'artifacts'))
        if record != {'path': record['path'], **fingerprint(file, include_path=False)}:
            from deep_sky_siril_session import _PRE_SUBJECT_MASK_BINDINGS, _PRE_LOCAL_CONTRAST_MASK_BINDINGS
            minimum_predecessor = (name == 'helper' and payload['execution_policy'] == _PRE_LOCAL_CONTRAST_MASK_BINDINGS['execution_policy']
                and record == {'path':'scripts/deep_sky_siril_local_contrast.py', **_PRE_LOCAL_CONTRAST_MASK_BINDINGS['helpers']['deep_sky_siril_local_contrast.py']})
            mask_predecessor = (name == 'helper' and payload['execution_policy'] == _PRE_SUBJECT_MASK_BINDINGS['execution_policy']
                and record == {'path':'scripts/deep_sky_siril_local_contrast.py', **_PRE_SUBJECT_MASK_BINDINGS['helpers']['deep_sky_siril_local_contrast.py']})
            outgoing = minimum_predecessor or mask_predecessor or (name == 'helper' and hdr_policy is None and diagnostic_only
                        and known_execution_policy(payload['execution_policy'])
                        and record == {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRE_HDR_HELPER})
            if not outgoing and (name != 'helper' or diagnostic_only
                or record not in [{'path': 'scripts/deep_sky_siril_local_contrast.py', **h}
                                  for h in (LEGACY_HELPER, PRIOR_HELPER, STRICT_TEXTURE_HELPER)]):
                fail('Local contrast parameter binding changed: '+name)
            legacy_helper = record == {'path': 'scripts/deep_sky_siril_local_contrast.py', **LEGACY_HELPER}
            prior_helper = record == {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRIOR_HELPER}
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if runtime != bindings['runtime'] or not fingerprint_matches(runtime['fingerprint']):
        fail('Local contrast frozen runtime changed')
    lineage = replay_lineage(session, session/bindings['source']['path'], bindings['lineage'],
                             policy=MULTISCALE_POLICY, hdr_policy=hdr_policy)
    methods = method_eligibility(lineage) if hdr_policy == HDR_POLICY else None
    if report.get('method_eligibility') != methods:
        fail('Local contrast method eligibility changed')
    request = strict_json_bytes((session/bindings['request']['path']).read_bytes(), document='local contrast request')
    if prior_helper and request['schema'] == PROTECTED_REQUEST:
        fail('Prior helpers cannot authorize protected requests')
    mask_support = ('source_exclusion_v2' if payload['execution_policy'].get('local_contrast_mask') == 'minimum_source_support_v1'
                    else 'source_exclusion_v1')
    expected = request_candidates(request, fine_off=bool(report.get('fallback_from')), mask_support=mask_support)
    if request['schema'] == SPATIAL_REQUEST:
        from deep_sky_siril_subject_mask import load as load_subject_mask
        load_subject_mask(session,session/bindings['source']['path'],request['subject_mask'])
    if methods:
        expected = eligible_candidates(expected, methods)
    if legacy_helper and request['schema'] != REQUEST_SCHEMA:
        fail('Legacy local contrast helpers cannot authorize proposal requests')
    actual_regions = strict_json_bytes((session/bindings['regions']['path']).read_bytes(), document='local contrast regions')
    if report.get('region_document') != actual_regions:
        fail('Local contrast report regions changed')
    source_pixels, _ = _source(session/bindings['source']['path'])
    if (report['manual_lookup'] != request['manual_lookup']
        or report['eligibility'] != texture(energies(source_pixels, actual_regions['regions']), diagnostic_only=diagnostic_only)):
        fail('Local contrast source eligibility or manual evidence changed')
    if report.get('coarse_context_available') is not True or report['coarse_parent_amplitudes'] != energies(source_pixels, actual_regions['regions'], scales=(16,)):
        fail('Local contrast coarse-scale baseline changed')
    candidates = report['candidates']
    if set(candidates) != set(expected):
        fail('Local contrast candidates must match the bound finite request')
    for key, row in candidates.items():
        if row['parameters'] != expected[key]:
            fail('Local contrast trial parameters changed')
        variant_parameters(row['parameters'])
        trial = path.parent/key
        primary = trial/'artifacts'/f'{report["run_id"]}-{key}.fit'
        if row['output'] != _record(session, primary) or row['local_preview'] != (trial/'local.png').relative_to(session).as_posix():
            fail('Local contrast trial output or local preview changed')
        if {record['path'] for record in row['evidence']} != {file.relative_to(session).as_posix() for file in trial.rglob('*') if file.is_file()}:
            fail('Local contrast trial evidence is incomplete')
        for record in row['evidence']:
            file = bound_file(session, record['path'], ('reports',))
            if record != _record(session, file):
                fail('Local contrast trial evidence changed: '+record['path'])
        values, _ = _source(primary)
        if row['amplitudes'] != energies(values, actual_regions['regions']):
            fail('Local contrast trial measurements changed')
    if report.get('fallback_from'):
        earlier = report['fallback_from']
        old = bound_file(session, earlier['path'], ('reports',))
        if earlier != _record(session, old):
            fail('Local contrast fallback evidence changed')
        parent = load_parameters(session, earlier['path'])
        if parent.get('fallback_from') or any(bindings[k] != parent['bindings'][k] for k in ('source', 'regions', 'runtime')):
            fail('Only one fine-off revision from the same parent and regions is allowed')
        parent_request = load_json(bound_file(session, parent['bindings']['request']['path'], ('reports',)))
        if request_candidates(request) != request_candidates(parent_request):
            fail('Fine-off revision must retain the original mask and proposed parameters')
        if report.get('fallback_reviews') != fallback_reviews(session, parent, earlier):
            fail('Fine-off rejection reviews changed')
    return report


def parameter_binding(session, source, regions, text):
    lines = [line.split(':', 1)[1] for line in (text or '').splitlines() if line.startswith('# local-contrast-parameters:')]
    if len(lines) != 1:
        fail('Explicit local contrast methods require one bound parameter report')
    value = strict_json_bytes(lines[0].encode(), document='local contrast parameter declaration')
    if not isinstance(value, dict) or set(value) != {'path', 'candidate'}:
        fail('Invalid local contrast parameter reference')
    report = load_parameters(session, value['path'])
    if (source != session/report['bindings']['source']['path'] or regions != report['bindings']['regions']['path']
        or value['candidate'] not in report['candidates']):
        fail('Local contrast report source, regions or candidate changed')
    declared = strict_json_bytes(next(line.split(':', 1)[1] for line in text.splitlines() if line.startswith('# local-contrast:')).encode(), document='local contrast')
    declared.pop('regions')
    if declared != report['candidates'][value['candidate']]['parameters']:
        fail('Formal local contrast parameters differ from the trial report')
    return {**_record(session, session/value['path']), 'candidate': value['candidate']}


def parameter_review_paths(session, reference):
    report = load_parameters(session, reference)
    paths = {reference, *[row['local_preview'] for row in report['candidates'].values()]}
    if report.get('fallback_from'):
        paths.update(parameter_review_paths(session, report['fallback_from']['path']))
    return paths


def measure_native(session, source, primary, binding):
    parameter = binding['parameters']
    file = bound_file(session, parameter['path'], ('reports',))
    if {k: parameter[k] for k in ('path', 'sha256', 'size')} != _record(session, file):
        fail('Local contrast parameter report changed after binding')
    report = load_parameters(session, parameter['path'])
    row = report['candidates'][parameter['candidate']]
    paths = binding['native']['files']
    read = lambda name: read_scientific_pixels(bound_file(session, paths[name], ('artifacts', 'reports')))[0]
    before, _ = _source(source)
    after, _ = _source(primary)
    lum, enhanced, mask, gain = [np.asarray(read(n), dtype=np.float64)[0] for n in ('l', 'l-enhanced', 'mask', 'gain')]
    expected_l = np.einsum('c,cyx->yx', [.2126, .7152, .0722], before.astype(float))
    error = lambda a, b: float(np.max(np.abs(a-b)))
    errors = {'luminance': error(lum, expected_l), 'rgb_common_gain': error(after, before.astype(float)*gain)}
    errors['trial_equivalence'] = error(after, _source(session/row['output']['path'])[0])
    if (not np.isfinite(gain).all() or float(gain.min()) <= 0 or not np.isfinite(mask).all()
        or mask.min() < -1e-6 or mask.max() > 1+1e-6):
        fail('Local contrast requires a finite positive common gain and a unit protection mask')
    p = row['parameters']
    errors['gain'] = error(gain, 1+p.get('strength', 1.)*mask*(enhanced-lum)/np.maximum(lum, 1e-6))
    if 'mask_support' in p:
        if p.get('subject_mask'):
            a,b,c,d = 0.,1.,p['high_start'],p['high_end']
        else:
            a,b,c,d = [p[k] for k in BOUNDS]
        smooth = lambda x: np.clip(x, 0, 1)**2 * (3-2*np.clip(x, 0, 1))
        base = read('mask-base')[0]
        blurred = read('mask-smoothed')[0]
        expected = smooth((lum-a)/(b-a)) * (1-smooth((before.max(axis=0)-c)/(d-c)))
        if p.get('subject_mask'):
            expected = 1-smooth((before.max(axis=0)-c)/(d-c))
        errors['mask_base'] = error(base, expected)
        minimum = p['mask_support'] == 'source_exclusion_v2'
        effective = np.minimum(base, blurred) if minimum else base*blurred
        if p.get('subject_mask'):
            from deep_sky_siril_subject_mask import weights
            space,_ = weights(session,source,p['subject_mask'])
            effective *= space
        errors['mask_minimum' if minimum else 'mask_product'] = error(mask, effective)
        excluded = (mask == 0) | (base == 0) | ((space == 0) if p.get('subject_mask') else False)
        if excluded.any() and not np.array_equal(after[:, excluded], before[:, excluded]):
            fail('Protected local contrast changed excluded scientific pixels')
    if p['method'] == 'wavelet':
        planes = [np.asarray(read(f'plane-{i}'), dtype=np.float64)[0] for i in range(1, 7)]
        errors['identity'] = error(read('identity')[0], lum)
        errors['layer_closure'] = error(sum(planes), lum)
        errors['weighted_reconstruction'] = error(sum(c*a for c, a in zip(p['coefficients'], planes)), enhanced)
    if any(not np.isfinite(e) or e > 2e-6 for e in errors.values()):
        fail('Native local contrast arithmetic or reconstruction changed: '+repr(errors))
    # Mask multiplication can move energy into coarse scales even with unity coarse coefficients.
    coarse = energies(after, report['region_document']['regions'], scales=(16,)) if report.get('coarse_context_available') else []
    spatial_checks = {}
    if p.get('subject_mask'):
        from deep_sky_siril_subject_mask import diagnostics
        spatial_checks = {'subject_mask':diagnostics(space,mask,before,after,report['region_document']['regions'])}
    return {**spatial_checks, 'method': p['method'], 'max_errors': errors, 'positive_common_gain': True,
            'gain_range': [float(gain.min()), float(gain.max())],
            'new_shadow_crossings': [int(np.count_nonzero((a > 0) & (b <= 0))) for a, b in zip(before, after)],
            'new_highlight_crossings': [int(np.count_nonzero((a < 1) & (b >= 1))) for a, b in zip(before, after)],
            'coarse_scale_amplitudes': coarse, 'coarse_parent_amplitudes': report.get('coarse_parent_amplitudes', []),
            'coarse_note': 'Gaussian sigma=16 diagnostic; protected coefficients do not guarantee zero final coarse drift'}


def _accepted_source(session, payload, source):
    from deep_sky_siril_processing import source_state
    import deep_sky_siril_core as core
    state = source_state(session, payload, source)
    if state['domain'] != 'nonlinear' or not state.get('separation_run') or not state.get('run'):
        fail('Local contrast solve requires an accepted nonlinear RGB starless parent')
    for run in (state['run'], next(r for r in state['lineage'] if r['id'] == state['separation_run'])):
        core._accepted_review(session, run['id'])
    lineage_binding(session, source, state['lineage'], policy=MULTISCALE_POLICY,
                    hdr_policy=payload['execution_policy'].get('local_contrast_hdr'))
    return state


def fallback_reviews(session, report, reference):
    import deep_sky_siril_core as core
    records = []
    for candidate, row in report['candidates'].items():
        if row['parameters']['method'] != 'wavelet':
            continue
        run_id = report['run_id']+'-'+candidate
        run = load_json(bound_file(session, f'runs/{run_id}.json', ('runs',)))
        binding = run.get('local_contrast_guard', {}).get('parameters', {})
        if binding != {**reference, 'candidate': candidate}:
            fail('Fine-off requires the rejected wavelet siblings from the original trial report')
        reviews = [p for p in (session/'reviews').glob('*.json') if load_json(p).get('run_id') == run_id]
        if len(reviews) != 1:
            fail('Fine-off requires one actual rejection review per wavelet candidate')
        review = core._validate_review(session, str(reviews[0]), run_id)[0]
        if review['verdict'] != 'reject' or review['gates']['background'] == 'pass':
            fail('Fine-off is only allowed after all compared wavelet background reviews reject')
        records.append(_record(session, reviews[0]))
    return records


def _local_panel(session, source, candidate, regions, output):
    from PIL import Image, ImageDraw
    before, _ = _source(source); after, _ = _source(candidate)
    selected = []
    for role in ('background', 'faint', 'core', 'stars'):
        selected += [r for r in regions if r['role'] == role][:2 if role == 'faint' else 1]
    width = 720; height = 32+len(selected)*220
    canvas = Image.new('RGB', (width, height), '#101418'); draw = ImageDraw.Draw(canvas)
    draw.text((12, 8), 'Parent / candidate: identical linear display, native-pixel crops', fill='white')
    h = before.shape[1]
    for i, region in enumerate(selected):
        x0, y0, x1, y1 = region['rect']
        for j, data in enumerate((before, after)):
            cut = data[:, h-y1:h-y0, x0:x1]
            image = Image.fromarray(np.uint8(np.clip(cut.transpose(1, 2, 0)[::-1], 0, 1)*255))
            image.thumbnail((344, 190))
            canvas.paste(image, (j*360+8, i*220+56))
            draw.text((j*360+8, i*220+34), region['id']+(' parent' if j == 0 else ' candidate'), fill='white')
    canvas.save(output)


def solve(session_value, request_value, run_id, timeout=1800, *, fallback_from=None, reason=None):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    from deep_sky_siril_dualband import _native
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if (payload['execution_policy'].get('local_contrast') != MULTISCALE_POLICY
        or not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None
        or type(timeout) not in (int, float) or not 1 <= timeout <= 86400):
        fail('A current session, valid run ID and timeout are required')
    request_file = session_path(session, request_value, must_exist=True, allowed_roots=('reports',))
    request = strict_json_bytes(request_file.read_bytes(), document='local contrast request')
    mask_support = ('source_exclusion_v2' if payload['execution_policy'].get('local_contrast_mask') == 'minimum_source_support_v1'
                    else 'source_exclusion_v1')
    choices = request_candidates(request, mask_support=mask_support)
    if not execution_policy_is_current(payload):
        fail('New local contrast proposals require the current frozen execution policy')
    source = bound_file(session, request['source'], ('artifacts',))
    state = _accepted_source(session, payload, source)
    hdr_policy = payload['execution_policy'].get('local_contrast_hdr')
    methods = method_eligibility(state['lineage']) if hdr_policy == HDR_POLICY else None
    if methods:
        choices = eligible_candidates(choices, methods)
    doc, region = _regions(session, source, request['regions'])
    coarse_available = all(min(r['rect'][:2]) >= 64 and r['rect'][2] <= doc['reference']['width']-64
        and r['rect'][3] <= doc['reference']['height']-64 for r in doc['regions'] if r['role'] in {'background', 'faint'})
    if not coarse_available:
        fail('Finite comparison requires real 64-pixel ROI context for the coarse-scale drift diagnostic')
    before, selection = _source(source)
    if request['schema'] == SPATIAL_REQUEST:
        from deep_sky_siril_subject_mask import weights
        space,_ = weights(session,source,request['subject_mask'])
        if not np.any(space*(before.max(axis=0) < request['mask']['high_end'])):
            directory = session_path(session,f'reports/{run_id}',allowed_roots=('reports',))
            if directory.exists():
                fail('Trial evidence already exists; use a new run ID')
            directory.mkdir()
            path = directory/'parameters.json'
            atomic_write_json(path,{'status':'skipped','reason':'No effective spatial coverage','request':_record(session,request_file)})
            return {'status':'skipped','parameters':str(path),'candidates':[]}
    eligible = texture(energies(before, doc['regions']),
                       diagnostic_only=payload['execution_policy'].get('subject_enhancement') == 'benefit_first_v1')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime['fingerprint']):
        fail('A frozen compatible Siril runtime is required')
    earlier = None
    rejected_reviews = []
    if fallback_from:
        fallback_from = session_path(session, fallback_from, must_exist=True, allowed_roots=('reports',)).relative_to(session).as_posix()
        parent = load_parameters(session, fallback_from)
        if (parent.get('fallback_from') or parent['bindings']['source'] != _record(session, source)
            or parent['bindings']['regions'] != region or not isinstance(reason, str) or not reason.strip()):
            fail('Fine-off revision requires the original source/regions and actual high-frequency rejection evidence')
        parent_request = load_json(bound_file(session, parent['bindings']['request']['path'], ('reports',)))
        if request_candidates(request) != request_candidates(parent_request):
            fail('Fine-off revision must retain the original mask and proposed parameters')
        choices = request_candidates(request, fine_off=True, mask_support=mask_support)
        earlier = _record(session, bound_file(session, fallback_from, ('reports',)))
        rejected_reviews = fallback_reviews(session, parent, earlier)
        for other in (session/'reports').glob('*/parameters.json'):
            document = load_json(other)
            if document.get('schema') == PARAMETERS_SCHEMA and document.get('fallback_from') == earlier:
                fail('The single fine-off revision for this trial report already exists')
    elif reason:
        fail('A fallback reason requires the original trial report')
    directory = session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    if directory.exists() or directory.is_symlink():
        fail('Local contrast trial evidence already exists; use a new run ID')
    directory.mkdir()
    candidates = {}
    deadline = time.monotonic()+timeout
    for key, parameters in choices.items():
        trial = directory/key; trial.mkdir()
        # Recipes use the same declared artifact layout in trials and formal runs.
        (trial/'artifacts').mkdir(); (trial/'previews').mkdir()
        primary = trial/'artifacts'/f'{run_id}-{key}.fit'
        rows = recipe(trial, source, primary, parameters, mask_session=session)
        # PixelMath variables are relative to Siril's CWD, so run from the isolated recipe root.
        _native(trial, runtime, trial, 'trial', rows[:-1], primary, deadline)
        panel = trial/'local.png'; _local_panel(session, source, primary, doc['regions'], panel)
        arrays, _ = _source(primary)
        candidates[key] = {'parameters': parameters, 'output': _record(session, primary),
            'amplitudes': energies(arrays, doc['regions']),
            'local_preview': panel.relative_to(session).as_posix(),
            'evidence': [_record(session, p) for p in sorted(trial.rglob('*')) if p.is_file()]}
    bindings = {'source': _record(session, source), 'regions': region, 'request': _record(session, request_file),
        'runtime': runtime, 'helper': {'path': 'scripts/deep_sky_siril_local_contrast.py', **fingerprint(Path(__file__).resolve(), include_path=False)},
        'lineage': lineage_binding(session, source, state['lineage'], policy=MULTISCALE_POLICY, hdr_policy=hdr_policy)}
    report = {'schema': PARAMETERS_SCHEMA, 'policy': MULTISCALE_POLICY, 'run_id': run_id,
        'session_sha256': payload['session_sha256'], 'bindings': bindings, 'eligibility': eligible,
        'requires_visual_review': True, 'manual_lookup': request['manual_lookup'],
        'region_document': doc, 'coarse_context_available': coarse_available,
        'coarse_parent_amplitudes': energies(before, doc['regions'], scales=(16,)) if coarse_available else [],
        'candidates': candidates, 'fallback_from': earlier, 'fallback_reason': reason, 'fallback_reviews': rejected_reviews}
    if methods:
        report['method_eligibility'] = methods
    if sha256_file(source) != selection['sha256'] or _record(session, request_file) != bindings['request'] or _record(session, session/region['path']) != region:
        fail('Local contrast source, request or regions changed during trials')
    path = directory/'parameters.json'; atomic_write_json(path, report)
    load_parameters(session, path.relative_to(session).as_posix())
    return {'status': 'review_required', 'parameters': str(path), 'candidates': list(candidates)}


def generate(session_value, parameter_value, candidate):
    from deep_sky_siril_session import load_session, execution_policy_is_current
    from deep_sky_siril_processing import script_commands
    from deep_sky_siril_validation import _normalized_write
    session, payload, _ = load_session(session_value)
    if not execution_policy_is_current(payload):
        fail('New local contrast generation requires the current frozen execution policy')
    report = load_parameters(session, parameter_value)
    if candidate not in report['candidates']:
        fail('Choose one actual finite trial candidate')
    run_id = report['run_id']+'-'+candidate
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        fail('Generated run ID is too long')
    primary = session/'artifacts'/f'{run_id}.fit'
    source = session/report['bindings']['source']['path']
    parameters = report['candidates'][candidate]['parameters']
    declaration = {**parameters, 'regions': report['bindings']['regions']['path']}
    text = '\n'.join(['requires 1.4.4 1.5.0', 'set32bits', '# local-contrast: '+json.dumps(declaration),
        '# local-contrast-parameters: '+json.dumps({'path': str(Path(parameter_value).relative_to(session)) if Path(parameter_value).is_absolute() else parameter_value, 'candidate': candidate}),
        *recipe(session, source, primary, parameters)])+'\n'
    expected = set()
    for row in script_commands(text):
        if row[0] in {'save', 'savejpg'}: expected.add(_normalized_write(row[0], row[1]))
        elif row[0] == 'split': expected.update(_normalized_write('split', v) for v in row[1:])
        elif row[0] == 'rgbcomp': expected.add(primary)
    script = session/'scripts'/f'{run_id}.ssf'; provenance = script.with_suffix('.provenance.json')
    for path in (script, provenance, *expected):
        if path.exists() or path.is_symlink():
            fail('Local contrast outputs already exist; use a new run ID')
    with script.open('x') as stream: stream.write(text)
    atomic_write_json(provenance, {'schema': 'starun-siril.ssf-provenance.v1', 'contract_version': '1',
        'run_id': run_id, 'protocol': 'structure.local-contrast',
        'script': {'path': script.relative_to(session).as_posix(), 'sha256': sha256_file(script)},
        'references': protocol_reference_bindings('structure.local-contrast'),
        'command_policy': {'path': 'references/command-policy.json', 'sha256': sha256_file(SKILL_ROOT/'references/command-policy.json')},
        'manual_lookup': report['manual_lookup'], 'rationale': {
            'applicability': 'Same-parent finite native local contrast comparison on a trusted nonlinear starless branch',
            'parameter_choices': ['Frozen five-detail-plus-residual B-spline or conservative luminance CLAHE; actual visual review is required',
                                  json.dumps(parameters, sort_keys=True)]}})
    return {'script': str(script), 'source': str(source), 'primary_output': str(primary), 'expected_outputs': sorted(str(p) for p in expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('solve'); s.add_argument('--session', required=True); s.add_argument('--request', required=True)
    s.add_argument('--run-id', required=True); s.add_argument('--timeout', type=float, default=1800)
    s.add_argument('--fallback-from'); s.add_argument('--reason')
    g = sub.add_parser('generate'); g.add_argument('--session', required=True); g.add_argument('--parameters', required=True); g.add_argument('--candidate', required=True)
    args = parser.parse_args()
    try:
        result = solve(args.session, args.request, args.run_id, args.timeout, fallback_from=args.fallback_from, reason=args.reason) if args.command == 'solve' else generate(args.session, args.parameters, args.candidate)
        print(json.dumps(result, indent=2))
    except (ContractError, OSError, ValueError, KeyError, TimeoutError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
