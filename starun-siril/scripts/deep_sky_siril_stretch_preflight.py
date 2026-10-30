"""Read-only constraint and MTF mapping review with measured GHS diagnostics."""
import json
import numpy as np

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected

LEGACY_POLICY = 'whole_chain_review_v1'
V2_POLICY = 'curve_effect_review_v2'
POLICY = 'curve_effect_review_v3'
LEGACY_SCHEMA = 'starun-siril.stretch-constraint-preflight.v1'
SCHEMA = 'starun-siril.stretch-constraint-preflight.v2'
BRIGHTNESS = {'median', 'p01', 'p99', 'p999', 'max_rgb'}
MAPPED_STATISTICS = BRIGHTNESS | {'span'}
MAPPING_TOLERANCE = 32 * float(np.finfo(np.float32).eps)
AMPLIFICATION_FLOOR = 1e-12
LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)


def enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_constraint_preflight') in {
        LEGACY_POLICY, V2_POLICY, POLICY}


def effects_enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_constraint_preflight') in {V2_POLICY, POLICY}


def amplification_enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_constraint_preflight') == POLICY


def scientific(steps):
    return [{k: step[k] for k in ('method', 'factors', 'landmarks') if k in step} for step in steps]


def domain(variant):
    return variant if variant in {'ha', 'oiii'} else 'rgb'


def prefix_key(variant, steps):
    return domain(variant), json.dumps(scientific(steps), sort_keys=True)


def ghs_effects(before, after, regions, targets, input_binding, output_binding):
    """Measured changes are evidence for review, never a structural-benefit gate."""
    def change(a, b):
        relative = (b-a)/abs(a) if a != 0 else None
        return {'before': a, 'after': b, 'delta': b-a,
                'relative_delta': relative if relative is not None and np.isfinite(relative) else None}
    skies = [r['id'] for r in regions if r['role'] == 'background']
    background_before = float(np.median([before[key]['median'] for key in skies]))
    background_after = float(np.median([after[key]['median'] for key in skies]))
    rows = {}
    for region in regions:
        key = region['id']
        row = {stat: change(before[key][stat], after[key][stat]) for stat in sorted(MAPPED_STATISTICS)}
        if key in targets:
            direction = 1 if targets[key] == 'bright' else -1
            row['subject_contrast'] = change(direction*(before[key]['median']-background_before),
                                             direction*(after[key]['median']-background_after))
        rows[key] = row
    return {'diagnostic_only': True, 'requires_visual_review': True,
            'input': input_binding, 'output': output_binding, 'regions': rows}


def mtf_pixels(data, low, mid, high):
    low, mid, high = map(base.number, (low,mid,high))
    if not 0 <= low < high <= 1 or not 0 < mid < 1:
        base.fail('MTF projection requires valid finite input levels and midtones')
    x = np.clip((data-low)/(high-low), 0., 1.)
    numerator = (1.-mid)*x
    denominator = numerator+mid*(1.-x)
    mapped = np.divide(numerator, denominator, out=np.zeros_like(x), where=denominator != 0)
    return np.where(x >= 1., 1., mapped).astype(np.float32)


def mtf_slope(u, low, mid, high):
    """Analytic dT/du of the same MTF curve, evaluated in float64.

    T(x) = (1-m)x / ((1-m)x + m(1-x)) with x=(u-low)/(high-low), so
    dT/dx = m(1-m) / (m+(1-2m)x)^2.  Clamped inputs map to a flat segment and
    report a zero slope instead of a one-sided derivative.
    """
    low, mid, high = map(base.number, (low, mid, high))
    if not 0 <= low < high <= 1 or not 0 < mid < 1:
        base.fail('MTF slope requires valid finite input levels and midtones')
    u = float(u)
    if not np.isfinite(u):
        base.fail('MTF slope requires a finite input level')
    x = (u-low)/(high-low)
    if x <= 0. or x >= 1.:
        return 0., True
    denominator = mid + (1.-2.*mid)*x
    return float(mid*(1.-mid)/(denominator*denominator*(high-low))), False


def channel_medians(data, regions):
    """Per-channel input medians inside each ROI, in the native channel order."""
    height = data.shape[1]
    result = {}
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        values = data[:, height-y1:height-y0, x0:x1]
        result[region['id']] = [float(np.median(values[c])) for c in range(values.shape[0])]
    return result


def _slope_row(medians, low, mid, high):
    channels, clamped = [], False
    for u in medians:
        slope, limit = mtf_slope(u, low, mid, high)
        channels.append(slope)
        clamped = clamped or limit
    weights = LUMINANCE_WEIGHTS if len(channels) == 3 else (1.,) * len(channels)
    return {'input_median': [float(u) for u in medians], 'channels': channels,
            'luminance': float(sum(w*s for w, s in zip(weights, channels))), 'clamped': bool(clamped)}


def _span_row(before, after):
    before = None if before is None else float(before)
    after = None if after is None else float(after)
    if before is None or after is None or not np.isfinite(before) or not np.isfinite(after):
        return {'before': before, 'after': after, 'ratio': None, 'reason': 'span_unavailable'}
    if before <= AMPLIFICATION_FLOOR:
        return {'before': before, 'after': after, 'ratio': None, 'reason': 'zero_span_baseline'}
    return {'before': before, 'after': after, 'ratio': float(after/before), 'reason': None}


def _median(values):
    finite = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.median(finite)) if finite else None


def _aggregate(slopes, spans, regions, targets):
    background = [r['id'] for r in regions if r['role'] == 'background']
    subject = [key for key in targets if key in spans] or [r['id'] for r in regions if r['role'] == 'faint']
    rows = {
        'background': None if not background else {
            'slope': None if slopes is None else _median([slopes[k]['luminance'] for k in background if k in slopes]),
            'span_gain': _median([spans[k]['ratio'] for k in background if k in spans])},
        'subject': None if not subject else {
            'slope': None if slopes is None else _median([slopes[k]['luminance'] for k in subject if k in slopes]),
            'span_gain': _median([spans[k]['ratio'] for k in subject if k in spans])}}
    if not background:
        reason = 'no_background_roi'
    elif not subject:
        reason = 'no_subject_roi'
    else:
        reason = None
    ratios = {}
    for key in ('slope', 'span_gain'):
        base_value = (rows['background'] or {}).get(key)
        subject_value = (rows['subject'] or {}).get(key)
        ratios[key] = (float(subject_value/base_value)
                       if subject_value is not None and base_value is not None
                       and np.isfinite(base_value) and np.isfinite(subject_value)
                       and base_value > AMPLIFICATION_FLOOR else None)
    if reason is None and ratios['slope'] is None and ratios['span_gain'] is None:
        reason = 'background_gain_unmeasurable'
    return {'background': rows['background'], 'subject': rows['subject'],
            'subject_over_background': {**ratios, 'reason': reason}, 'reason': reason}


def mtf_amplification(values, before, regions, targets, medians, low, mid, high):
    slopes = {r['id']: _slope_row(medians[r['id']], low, mid, high) for r in regions}
    spans = {r['id']: _span_row(before[r['id']]['span'], values[r['id']]['span']) for r in regions}
    return {'diagnostic_only': True,
            'slope': {'source': 'analytic_mtf_derivative',
                      'evaluation': 'input_roi_median_per_channel',
                      'regions': slopes, 'reason': None},
            'span_gain': {'source': 'predicted_mtf', 'regions': spans, 'reason': None},
            'aggregate': _aggregate(slopes, spans, regions, targets)}


def ghs_amplification(diagnostics, regions, targets):
    if not diagnostics:
        return pending_amplification('pending_native_measurement')
    spans = {}
    for region in regions:
        row = diagnostics['regions'].get(region['id'], {}).get('span')
        spans[region['id']] = _span_row(None, None) if row is None else _span_row(row['before'], row['after'])
    return {'diagnostic_only': True,
            'slope': {'source': None, 'evaluation': None, 'regions': None,
                      'reason': 'ghs_has_no_analytic_derivative'},
            'span_gain': {'source': 'measured_ghs', 'regions': spans, 'reason': None},
            'aggregate': _aggregate(None, spans, regions, targets)}


def pending_amplification(reason):
    return {'diagnostic_only': True,
            'slope': {'source': None, 'evaluation': None, 'regions': None, 'reason': reason},
            'span_gain': {'source': None, 'regions': None, 'reason': reason},
            'aggregate': {'background': None, 'subject': None,
                          'subject_over_background': {'slope': None, 'span_gain': None, 'reason': reason},
                          'reason': reason}}


def suggest_mtf(session_value, source_value, regions_value, anchor_region, target_median):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    from deep_sky_siril_metrics import measure_regions
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload) or not base.optimization_enabled(payload):
        base.fail('MTF suggestions require a current Stage 7 optimization policy')
    source = (base.Path(payload['input']['path']) if str(source_value) == '@input' else
              base.session_path(session, source_value, must_exist=True, allowed_roots=('artifacts',)))
    full = base.accepted_parent(session, payload, source)
    regions_path = base.session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    if measure_regions(source, None, regions_path)['status'] != 'measured':
        base.fail('MTF suggestion regions must bind the current scientific parent')
    source_sha = base.sha256_file(source)
    region_binding = base.record(session, regions_path)
    regions = base.load_json(regions_path)['regions']
    matches = [r for r in regions if r['id'] == anchor_region and r['role'] == 'faint']
    target = base.number(target_median)
    if len(matches) != 1 or not 0 < target < 1:
        base.fail('MTF suggestion requires a faint anchor and an interior target median')
    data = base.pixels(source)
    full_data = base.pixels(full) if full is not None and not protected.full_is_diagnostic(payload) else None
    full_binding = base.record(session, full) if full_data is not None else None
    black = base.safe_blackpoint([data]+([full_data] if full_data is not None else []), headroom=True)
    x0, y0, x1, y1 = matches[0]['rect']
    height = data.shape[1]
    anchor = data[:, height-y1:height-y0, x0:x1]
    def median(mid):
        mapped = mtf_pixels(anchor, black, mid, 1.)
        lum = mapped[0] if len(mapped) == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], mapped)
        return float(np.median(lum))
    lo, hi = base.PROTOCOL_MIN_MTF, base.PROTOCOL_MAX_MTF
    low_value, high_value = median(hi), median(lo)
    def unchanged():
        if (base.sha256_file(source) != source_sha or base.record(session, regions_path) != region_binding
            or full_binding is not None and base.record(session, full) != full_binding):
            base.fail('MTF suggestion source or regions changed during prediction')
    result = {'schema': 'starun-siril.mtf-suggestion.v1', 'source': {'path': str(source_value), 'sha256': source_sha},
              'regions': region_binding, 'anchor_region': anchor_region,
              'target_median': target, 'blackpoint': black, 'attainable_median': [low_value, high_value],
              'requires_native_verification': True, 'requires_visual_review': True}
    if not low_value <= target <= high_value:
        unchanged()
        return {**result, 'status': 'no_solution'}
    for _ in range(32):
        mid = (lo+hi)/2
        if median(mid) > target:
            lo = mid
        else:
            hi = mid
    mid = (lo+hi)/2
    mapped = mtf_pixels(data, black, mid, 1.)
    counts = protected.endpoints(data, mapped)
    result = {**result, 'status': 'proposed', 'midtones': mid,
            'predicted_statistics': protected.statistics(mapped, regions),
            'predicted_endpoints': counts, 'predicted_clipping': base.clipping(mapped)}
    unchanged()
    return result


def previous_inputs(session, plan, goals, regions, *, effects=False):
    """Only an explicitly bound, verified same-source native prefix is a control."""
    revision = goals.get('revision')
    if not revision:
        return {}, []
    path = base.bound_file(session, revision['parameters'], ('reports', 'runs'))
    report = base.load_json(path)
    if report['status'] not in {'solved', 'ineligible'}:
        return {}, []
    request = report['request']
    if (request['source'] != plan['source'] or
        base.load_json(base.bound_file(session, request['regions']))['regions'] != regions):
        return {}, []
    derived = report['bindings'].get('derived_source')
    source = base.bound_file(session, derived) if derived else base.source_file(
        session, base.load_json(session/'session.json'), plan['source'])
    protected.verify(session, report, source, regions, incomplete=report['status'] != 'solved')
    variant = request['strategy']['variant']
    known, evidence = {}, []
    previous = request['source']
    before = protected.statistics(base.pixels(source), regions) if effects else None
    for index, trace in enumerate(report['steps'], 1):
        for trial in trace.get('trials', []):
            evidence.extend(trial.get('retained', {}).values())
        if trace.get('local_panel'):
            evidence.append(trace['local_panel'])
        chosen = trace.get('selected_trial')
        if chosen is not None:
            binding = trace['selected']['.fit']
            evidence.extend(trace['selected'].values())
        elif len(trace.get('trials', [])) == 1 and 'candidate.fit' in trace['trials'][0].get('retained', {}):
            binding = trace['trials'][0]['retained']['candidate.fit']
        else:
            continue
        value = protected.statistics(base.pixels(base.bound_file(session, binding)), regions)
        # Channel preparation/optional denoising is outside this strategy's step list.
        if not derived:
            row = {
                'kind': 'verified_native_prefix', 'reference': binding, 'statistics': value,
                'parameters': revision['parameters']}
            if effects:
                row['eligible'] = chosen is not None
                if trace.get('selected-full'):
                    row['full_reference'] = trace['selected-full']['.fit']
                if trace['method'] == 'ght':
                    row['effect_diagnostics'] = ghs_effects(before, value, regions, request['targets'], previous, binding)
            known[prefix_key(variant, request['operators'][:index])] = row
        previous, before = binding, value
    return known, evidence


def curve_effect(session, payload, plan, request, index, reference, known, regions, initial_data, cache):
    step = request['operators'][index-1]
    key = prefix_key(request['strategy']['variant'], request['operators'][:index])
    targets = request.get('targets') or {}
    result = {'method': step['method'], 'input': {k:v for k,v in reference.items()
              if k not in {'statistics','effect_diagnostics','full_reference'}}}
    if step['method'] == 'ght':
        measured = known.get(key, {}).get('effect_diagnostics')
        result = {**result, 'status': 'measured' if measured else 'pending_native_measurement',
                  'diagnostic_only': True, 'effect_diagnostics': measured}
        if amplification_enabled(payload):
            result['curve_amplification'] = ghs_amplification(measured, regions, targets)
        return result
    if reference['kind'] == 'unknown':
        result = {**result, 'status': 'pending_native_measurement'}
        if amplification_enabled(payload):
            result['curve_amplification'] = pending_amplification('pending_native_measurement')
        return result
    if key not in cache:
        data = initial_data if index == 1 else base.pixels(base.bound_file(session, reference['reference'], ('reports','artifacts')))
        full = None
        if plan['full_source'] and not protected.full_is_diagnostic(payload):
            binding = plan['full_source'] if index == 1 else reference.get('full_reference')
            if binding is None:
                result = {**result, 'status': 'pending_native_measurement'}
                if amplification_enabled(payload):
                    result['curve_amplification'] = pending_amplification('pending_native_measurement')
                return result
            full = base.pixels(base.bound_file(session, binding, ('reports','artifacts')))
        black = base.safe_blackpoint([data]+([full] if full is not None else []), headroom=base.optimization_enabled(payload))
        tokens = protected.proposal_tokens(step, black, {})
        mapped = mtf_pixels(data, *map(float, tokens[1:]))
        cache[key] = ({'command': tokens[0], 'parameters': tokens[1:]},
                      protected.statistics(mapped, regions), channel_medians(data, regions))
    parameters, values, medians = cache[key]
    rows = []
    for constraint in step['constraints']:
        statistic, region = constraint['statistic'], constraint['region']
        if statistic not in MAPPED_STATISTICS:
            continue
        value = values[region][statistic]
        tolerance = MAPPING_TOLERANCE * (2 if statistic == 'span' else 1)
        deviation = max(constraint['min']-value, value-constraint['max'], 0.)
        boundary = min(abs(value-constraint['min']), abs(value-constraint['max'])) <= tolerance
        rows.append({**constraint, 'input_value': reference['statistics'][region][statistic],
                     'mapped_value': value, 'deviation': deviation, 'tolerance': tolerance,
                     'status': 'outside_target' if deviation > tolerance else 'requires_native_verification' if boundary else 'within_target'})
    result = {**result, 'status': 'mapped', 'parameters': parameters, 'constraints': rows,
              'requires_native_verification': True}
    if amplification_enabled(payload):
        result['curve_amplification'] = mtf_amplification(
            values, reference['statistics'], regions, targets, medians,
            *map(float, parameters['parameters']))
    return result


def assess(session, payload, plan, goals, requests, issues=None):
    from deep_sky_siril_stretch_profiles import source_previews
    issues = list(issues or [])
    from deep_sky_siril_morphology import enabled as morphology_enabled, assess as assess_morphology
    morphology = assess_morphology(session,payload,plan,goals) if morphology_enabled(payload) else None
    if morphology is not None:
        issues.extend(morphology['issues'])
    regions = base.load_json(base.bound_file(session, plan['regions']))['regions']
    roles = {r['id']: r['role'] for r in regions}
    source = base.source_file(session, payload, plan['source'])
    effects = effects_enabled(payload)
    initial_data = base.pixels(source)
    initial = {'kind': 'actual_linear_parent', 'reference': plan['source'],
               'statistics': protected.statistics(initial_data, regions)}
    known, previous_evidence = previous_inputs(session, plan, goals, regions, effects=effects)
    allowed = source_previews(session, payload, plan['source']) + previous_evidence
    rows, groups, locations, warnings = [], {}, {}, []
    if payload.get('execution_policy', {}).get('evidence_admission') == 'source_bound_v1':
        for faint in sorted((r for r in regions if r['role'] == 'faint'), key=lambda r: r['id']):
            fx0, fy0, fx1, fy1 = faint['rect']
            for core in sorted((r for r in regions if r['role'] == 'core'), key=lambda r: r['id']):
                cx0, cy0, cx1, cy1 = core['rect']
                x0, y0, x1, y1 = max(fx0, cx0), max(fy0, cy0), min(fx1, cx1), min(fy1, cy1)
                if x0 < x1 and y0 < y1:
                    warnings.append({'code': 'faint_core_overlap', 'faint': faint['id'], 'core': core['id'],
                                     'intersection': [x0, y0, x1, y1],
                                     'faint_fraction': (x1-x0)*(y1-y0)/((fx1-fx0)*(fy1-fy0))})
    curves, cache = [], {}
    from deep_sky_siril_stretch_profiles import HDR_POLICY
    hdr = 'hdr' in goals and payload.get('execution_policy', {}).get('stretch_hdr') == HDR_POLICY
    for variant, request in requests.items():
        steps = [{'constraints': goals['protection']}] if hdr else request['operators']
        for index, step in enumerate(steps, 1):
            if hdr:
                reference = {'kind': 'unknown', 'reason': 'The native masked HDR blend has not run'}
            elif index == 1:
                # HOO extraction/optional denoising has not run: RGB statistics are not channel input statistics.
                reference = initial if domain(variant) == 'rgb' else {'kind': 'unknown', 'reason': 'Channel input has not been natively prepared'}
            else:
                reference = known.get(prefix_key(variant, request['operators'][:index-1]),
                    {'kind': 'unknown', 'reason': 'No verified same-source native prefix'})
                if effects and reference.get('eligible') is False:
                    reference = {'kind': 'unknown', 'reason': 'The measured prefix failed its scientific audit'}
            if effects and not hdr and step['method'] in {'mtf','ght'}:
                effect = curve_effect(session,payload,plan,request,index,reference,known,regions,initial_data,cache)
                if (payload.get('execution_policy', {}).get('evidence_admission') == 'source_bound_v1'
                    and plan['full_source'] and protected.full_is_diagnostic(payload)):
                    for row in effect.get('constraints', []):
                        if roles.get(row['region']) == 'stars':
                            row['diagnostic_only'] = True
                curves.append({'variant': variant, 'step': index, **effect})
                issues.extend({'code': 'mtf_mapping_outside_target', 'variant': variant, 'step': index, **row}
                              for row in effect.get('constraints',[]) if row['status'] == 'outside_target' and not row.get('diagnostic_only'))
            for constraint in step['constraints']:
                loc = {'variant': variant, 'step': index, 'region': constraint['region'], 'statistic': constraint['statistic']}
                key = tuple(loc.values())
                required = (index < len(steps) and constraint['statistic'] in BRIGHTNESS and constraint['min'] > 0)
                group = (*prefix_key(variant, request['operators'][:index]), constraint['region'],
                         constraint['statistic'], constraint['min'], constraint['max'])
                if required:
                    groups.setdefault(group, []).append(key)
                value = reference.get('statistics', {}).get(constraint['region'], {}).get(constraint['statistic'])
                row = {**loc, 'role': roles.get(constraint['region']), 'min': constraint['min'], 'max': constraint['max'],
                    'shared_protection': [r for r in goals['protection'] if
                        (r['region'], r['statistic']) == (constraint['region'], constraint['statistic'])],
                    'input': {k:v for k,v in reference.items() if k not in {'statistics','effect_diagnostics','full_reference'}}, 'input_value': value,
                    'requires_basis': required, 'basis': None}
                if value is not None and not constraint['min'] <= value <= constraint['max']:
                    warnings.append({**loc, 'code': 'input_outside_output_interval', 'input_value': value,
                                     'diagnostic_only': True})
                locations[key] = (row, group)
                rows.append(row)
    reviews = goals.get('constraint_review')
    if not isinstance(reviews, list):
        issues.append({'code': 'missing_constraint_review', 'reason': 'Declare constraint_review, including an empty list when no basis is needed'})
        reviews = []
    reviewed, seen = {}, set()
    for review in reviews:
        if (not isinstance(review, dict) or set(review) != {'variant', 'step', 'region', 'statistic', 'reason', 'evidence'}
            or any(not isinstance(review[k], str) or not review[k].strip() for k in ('variant','region','statistic','reason'))
            or type(review['step']) is not int or not isinstance(review['evidence'], list) or not review['evidence']):
            issues.append({'code': 'invalid_constraint_basis', 'reason': 'Each basis needs an exact location, reason and bound evidence'})
            continue
        loc = {k: review[k] for k in ('variant','step','region','statistic')}
        key = tuple(loc.values())
        if key in seen:
            issues.append({'code': 'duplicate_constraint_basis', **loc})
            continue
        seen.add(key)
        if key not in locations or not locations[key][0]['requires_basis']:
            issues.append({'code': 'misplaced_constraint_basis', **loc})
            continue
        valid = True
        for binding in review['evidence']:
            try:
                if not isinstance(binding, dict) or not any(binding.get('path') == e.get('path') and
                    binding.get('sha256') == e.get('sha256') for e in allowed):
                    base.fail('Basis must cite a committed parent preview or verified same-source prefix')
                base.bound_file(session, binding, ('reports','previews','artifacts'))
            except (base.ContractError, OSError, ValueError) as exc:
                issues.append({'code': 'invalid_basis_evidence', **loc, 'reason': str(exc)})
                valid = False
        if valid:
            reviewed[locations[key][1]] = review
    for group, keys in groups.items():
        for key in keys:
            row = locations[key][0]
            row['basis'] = reviewed.get(group)
            if row['basis'] is None:
                issues.append({'code': 'missing_intermediate_basis', **{k: row[k] for k in ('variant','step','region','statistic')},
                               'min': row['min'], 'max': row['max']})
    return {'schema': SCHEMA if effects else LEGACY_SCHEMA, 'status': 'needs_revision' if issues else 'ready',
        'source': plan['source'], 'regions': plan['regions'], 'goals': plan['goals'],
        'constraints': rows, 'issues': issues, 'warnings': warnings,
        **({'morphology':morphology} if morphology is not None else {}),
        **({'curve_effects': curves} if effects else {}),
        'output_reachability': 'partially_evaluated' if any(r['status']=='mapped' for r in curves) else 'not_predicted',
        'requires_visual_review': True}


def require_ready(report):
    if report['status'] != 'ready':
        error = base.ContractError('stretch_preflight_invalid',
            'Whole-chain constraint preflight needs revision: '+json.dumps(report['issues'], ensure_ascii=False))
        error.preflight = report
        raise error


def verify(session, payload, plan, goals, requests):
    path = base.bound_file(session, plan['constraint_preflight'])
    if path != session/'reports'/plan['run_id']/'constraint-preflight.json':
        base.fail('Misplaced whole-chain constraint preflight')
    report = assess(session, payload, plan, goals, requests)
    require_ready(report)
    if base.load_json(path) != report:
        base.fail('Whole-chain constraint preflight differs from its frozen strategy')


def preflight_requests(*args, **kwargs):
    from deep_sky_siril_stretch_profiles import init_requests
    try:
        return init_requests(*args, **kwargs, preflight=True)
    except (base.ContractError, OSError, ValueError) as exc:
        return {'schema': SCHEMA, 'status': 'needs_revision', 'constraints': [],
                'issues': [{'code': 'invalid_strategy', 'reason': str(exc)}], 'warnings': []}
